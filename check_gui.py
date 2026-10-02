# -*- coding: utf-8 -*-
"""
图形界面入口：基于 tkinter 的停车场异常车辆检测工具。

界面在 tkinter/ttk 原生能力上做了轻量现代化（零第三方依赖，不增加打包体积）：
  - clam 主题 + 自定义浅色配色（主色与托盘图标一致的蓝色）；
  - 微软雅黑 UI 字体、Windows 高分屏 DPI 感知，文字清晰不发虚；
  - 卡片式分组、表单栅格对齐、悬浮提示（Tooltip）；
  - 底部全局操作栏：开始检测 / 清空输出 / 打开输出目录与状态显示，
    任意页签下均可用，手动检测时自动切换到输出页。

允许在图形界面中配置：
  - 日志文件 / 目录路径
  - CSV 输出目录
  - 各项判定参数（判定窗口、最小停车时间偏差、入场去重窗口）
  - 定时任务（基于 APScheduler）：间隔运行 / 每天固定时间，
    配置项（间隔值/单位、HH:MM、开关）均可直接编辑

配置按分页隔离：路径配置 / 判定参数 / 定时任务 / 数据库上传 / 运行与输出。

关闭窗口后自动收纳到系统托盘（pystray）继续后台运行，
托盘菜单可重新打开主窗口或完全退出。

后台线程执行与命令行一致的处理流程：
  parse_log → find_anomalies → output_results
运行期间的控制台输出会实时回显到界面文本区（只读）。
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
    DB_BATCH_SIZE,
    UPLOAD_ENABLED,
    CF_ACCOUNT_ID,
    CF_DATABASE_ID,
    CF_API_TOKEN,
)
from parkcheck.scheduler import (
    SchedulerManager,
    interval_to_seconds,
    validate_daily_time,
)
from parkcheck.cli import (
    check_upload_cfg,
    list_log_files,
    run_detection_round,
)


# ---------------- 主题配色（浅色，主色与托盘图标一致） ----------------
COLORS = {
    "bg": "#edf0f5",            # 窗口底色（比卡片略深，衬托白色卡片层次）
    "card": "#ffffff",          # 卡片底色
    "border": "#dfe3ea",        # 边框/分隔线
    "text": "#1f2430",          # 主文字
    "secondary": "#6b7280",     # 次要文字（提示）
    "accent": "#2f6fed",        # 主色
    "accent_hover": "#2a63d6",  # 主色：悬停
    "accent_down": "#2456c2",   # 主色：按下
    "accent_disabled": "#a9c0ec",  # 主色：禁用
    "success": "#0f8a3d",       # 成功/运行中状态
    "select": "#cfe0ff",        # 文本选区
}

_FONT_FAMILY = "Microsoft YaHei UI"   # 界面字体族（_apply_style 运行时按系统可用性回退）


def _set_app_user_model_id():
    """Windows：为进程声明独立的 AppUserModelID。

    不设置时任务栏按 python.exe 归组，任务栏与通知弹窗都显示 Python 默认图标；
    设置后 Windows 以本应用身份展示窗口图标。必须在创建任何窗口之前调用。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("parkcheck.gui")
    except Exception:
        pass


def _enable_windows_dpi_awareness():
    """Windows：声明系统 DPI 感知，高分屏上文字清晰不发虚。

    必须在创建任何窗口之前调用；声明失败（旧系统）静默忽略，
    效果等同未声明，界面仍可用。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 系统 DPI 感知
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _apply_style(root):
    """配置全局视觉主题：字体、配色与 ttk 控件样式（基于 clam，跨平台可定制）。

    返回等宽字体（供输出区使用）；选定的界面字体族写入模块级 _FONT_FAMILY。
    仅样式配置，无运行期开销。
    """
    import tkinter.font as tkfont

    try:
        families = set(tkfont.families(root))
    except Exception:
        families = set()

    # 中文界面优先使用微软雅黑（Windows 自带），缺失时逐级回退
    global _FONT_FAMILY
    family = "Microsoft YaHei UI"
    if families and family not in families:
        family = next(
            (c for c in ("微软雅黑", "Microsoft YaHei", "PingFang SC",
                         "Noto Sans CJK SC", "Segoe UI") if c in families),
            tkfont.nametofont("TkDefaultFont").actual("family"),
        )
    _FONT_FAMILY = family

    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                 "TkCaptionFont", "TkTooltipFont"):
        try:
            tkfont.nametofont(name).configure(family=family, size=9)
        except Exception:
            pass

    mono = ("Consolas", 9) if "Consolas" in families else ("Courier New", 9)

    c = COLORS
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=c["bg"], foreground=c["text"], font=(family, 9))
    style.configure("TFrame", background=c["bg"])
    style.configure("Card.TFrame", background=c["card"])
    style.configure("TLabel", background=c["bg"], foreground=c["text"])
    style.configure("Form.TLabel", background=c["card"], foreground=c["text"])
    style.configure("Hint.TLabel", background=c["bg"], foreground=c["secondary"],
                    font=(family, 8))
    style.configure("Card.Hint.TLabel", background=c["card"], foreground=c["secondary"],
                    font=(family, 8))
    style.configure("Status.TLabel", background=c["card"], foreground=c["secondary"],
                    font=(family, 8))
    style.configure("Card.Status.TLabel", background=c["card"], foreground=c["secondary"])
    style.configure("Card.StatusOK.TLabel", background=c["card"], foreground=c["success"])

    # 卡片分组：白底 + 1px 浅色描边，标题加粗
    style.configure("Card.TLabelframe", background=c["card"], bordercolor=c["border"],
                    relief="solid", borderwidth=1)
    style.configure("Card.TLabelframe.Label", background=c["card"], foreground=c["text"],
                    font=(family, 9, "bold"))

    # 输入类控件：白底、浅描边、聚焦时主色描边
    for name in ("TEntry", "TSpinbox", "TCombobox"):
        style.configure(name, fieldbackground=c["card"], foreground=c["text"],
                        bordercolor=c["border"], lightcolor=c["border"],
                        darkcolor=c["border"], insertcolor=c["text"], padding=(7, 4))
        style.map(name,
                  bordercolor=[("focus", c["accent"])],
                  lightcolor=[("focus", c["accent"])],
                  fieldbackground=[("disabled", "#f2f3f6"), ("readonly", c["card"])])
    style.configure("TSpinbox", arrowcolor=c["secondary"], background="#e7eaf0")
    style.configure("TCombobox", arrowcolor=c["secondary"], background="#e7eaf0")

    # Combobox 弹出列表是经典 Listbox，走 option 覆盖配色
    root.option_add("*TCombobox*Listbox.background", c["card"])
    root.option_add("*TCombobox*Listbox.foreground", c["text"])
    root.option_add("*TCombobox*Listbox.font", (family, 9))
    root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    # 按钮：普通白底描边，Accent 为主色实心（唯一主动作）
    style.configure("TButton", background=c["card"], foreground=c["text"],
                    bordercolor=c["border"], lightcolor=c["card"], darkcolor=c["card"],
                    focuscolor=c["accent"], padding=(12, 5), borderwidth=1)
    style.map("TButton",
              background=[("disabled", "#f2f3f6"), ("pressed", "#e6eaf2"),
                          ("active", "#f2f5fa")],
              foreground=[("disabled", "#9aa2af")],
              bordercolor=[("active", "#c2cbd9")])
    style.configure("Accent.TButton", background=c["accent"], foreground="#ffffff",
                    bordercolor=c["accent"], lightcolor=c["accent"], darkcolor=c["accent"],
                    focuscolor="#ffffff", padding=(18, 5))
    style.map("Accent.TButton",
              background=[("disabled", c["accent_disabled"]), ("pressed", c["accent_down"]),
                          ("active", c["accent_hover"])],
              foreground=[("disabled", "#eef2fc")],
              bordercolor=[("active", c["accent_hover"]),
                           ("disabled", c["accent_disabled"])])

    # 复选框 / 单选框：白底 + 主色选中态（显式覆盖到 Card.* 派生样式）
    for name in ("TCheckbutton", "TRadiobutton",
                 "Card.TCheckbutton", "Card.TRadiobutton"):
        style.configure(name, background=c["card"], foreground=c["text"],
                        focuscolor=c["accent"], indicatorbackground=c["card"],
                        indicatorforeground="#ffffff", padding=2)
        style.map(name,
                  background=[("active", c["card"])],
                  foreground=[("disabled", "#9aa2af")],
                  indicatorcolor=[("selected", c["accent"]), ("pressed", c["card"])],
                  indicatorbackground=[("selected", c["accent"]),
                                       ("pressed", c["border"]),
                                       ("active", c["card"]),
                                       ("disabled", "#f2f3f6")])

    # 页签：无边框扁平样式，选中页签白底、主色加粗文字
    style.configure("TNotebook", background=c["bg"], borderwidth=0,
                    bordercolor=c["bg"], lightcolor=c["bg"], darkcolor=c["bg"],
                    tabmargins=(12, 10, 12, 0))
    style.configure("TNotebook.Tab", padding=(18, 7), background=c["bg"],
                    foreground=c["secondary"], borderwidth=0,
                    bordercolor=c["bg"], lightcolor=c["bg"], darkcolor=c["bg"],
                    font=(family, 9))
    style.map("TNotebook.Tab",
              background=[("selected", c["card"]), ("active", "#e9edf4")],
              foreground=[("selected", c["accent"]), ("active", c["text"])],
              font=[("selected", (family, 9, "bold"))])

    # 分隔线与滚动条
    style.configure("TSeparator", background=c["border"])
    style.configure("TScrollbar", background="#d4d9e1", troughcolor=c["bg"],
                    bordercolor=c["bg"], arrowcolor=c["secondary"], relief="flat")
    style.map("TScrollbar",
              background=[("active", "#c3cad4"), ("pressed", "#b3bccb")],
              arrowcolor=[("active", c["text"])])

    return mono


class _ToolTip:
    """轻量级悬浮提示：悬停约 0.5 秒后弹出深色气泡说明。

    纯 tkinter 实现，气泡按需创建、离开即销毁，几乎无开销。
    """

    def __init__(self, widget, text, delay=500, wraplength=360):
        self._widget = widget
        self._text = text
        self._delay = delay
        self._wraplength = wraplength
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self._widget.after(self._delay, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip is not None:
            return
        x = self._widget.winfo_rootx() + 10
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 6
        self._tip = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        try:
            tw.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(
            tw, text=self._text, justify="left", wraplength=self._wraplength,
            background="#2b2f36", foreground="#f5f6f8",
            padx=10, pady=6, font=(_FONT_FAMILY, 8),
        ).pack()
        tw.wm_geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


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
    """主窗口：分页配置（路径 / 判定参数 / 定时任务 / 上传）+ 运行输出；关闭后收纳系统托盘。"""

    def __init__(self, root):
        self.root = root
        root.title("停车场异常车辆检测")

        # 主题与字体须在创建任何控件前配置
        self._mono_font = _apply_style(root)

        # 高分屏按系统缩放比例放大默认窗口尺寸，并限制在屏幕范围内居中
        self._scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self._hint_wrap = int(640 * self._scale)
        self._tip_wrap = int(360 * self._scale)
        win_w, win_h = int(780 * self._scale), int(680 * self._scale)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        win_w, win_h = min(win_w, int(sw * 0.92)), min(win_h, int(sh * 0.9))
        root.geometry(f"{win_w}x{win_h}+{(sw - win_w) // 2}+{max(0, (sh - win_h) // 3)}")
        root.minsize(int(680 * self._scale), int(560 * self._scale))

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
        self._update_schedule_status()   # 同步底部状态栏初始显示

    # ---------------- 界面搭建 ----------------
    def _card(self, parent, title, stretch=True):
        """构造卡片容器（白底描边的 Labelframe 分组），返回内部内容 frame。

        stretch=True 时第 1 列可伸展（输入框占满卡片宽度）；
        放 Spinbox 等窄控件的卡片传 stretch=False，让右侧提示紧贴控件。
        """
        card = ttk.Labelframe(parent, text=title, style="Card.TLabelframe",
                              padding=(14, 10))
        card.pack(fill="x", pady=(0, 10))
        card.columnconfigure(1, weight=1 if stretch else 0)
        return card

    def _entry_row(self, parent, row, label, default, show=None, tooltip=None):
        """栅格表单行：右对齐标签 + 可伸展输入框，返回输入框变量。"""
        lbl = ttk.Label(parent, text=label, style="Form.TLabel")
        lbl.grid(row=row, column=0, sticky="e", padx=(0, 10), pady=6)
        if tooltip:
            _ToolTip(lbl, tooltip, wraplength=self._tip_wrap)
        var = tk.StringVar(value=str(default))
        ttk.Entry(parent, textvariable=var, show=show or "").grid(
            row=row, column=1, sticky="we", pady=6)
        return var

    def _build_widgets(self):
        # 顶部菜单栏：说明 / 配置（含导出导入与退出程序）
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

        # 底部全局操作栏（先 pack 到 bottom，上方内容区占满剩余空间）
        self._build_action_bar()
        ttk.Separator(self.root, orient="horizontal").pack(side="bottom", fill="x")

        # 分页：路径配置 / 判定参数 / 定时任务 / 数据库上传 / 运行与输出
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        tab_paths = ttk.Frame(self.notebook, padding=(12, 10, 12, 4))
        tab_params = ttk.Frame(self.notebook, padding=(12, 10, 12, 4))
        tab_sched = ttk.Frame(self.notebook, padding=(12, 10, 12, 4))
        tab_upload = ttk.Frame(self.notebook, padding=(12, 10, 12, 4))
        tab_run = ttk.Frame(self.notebook, padding=(12, 10, 12, 10))
        self.notebook.add(tab_paths, text="路径配置")
        self.notebook.add(tab_params, text="判定参数")
        self.notebook.add(tab_sched, text="定时任务")
        self.notebook.add(tab_upload, text="数据库上传")
        self.notebook.add(tab_run, text="运行与输出")
        self._run_tab_index = len(self.notebook.tabs()) - 1

        self._build_paths_tab(tab_paths)
        self._build_params_tab(tab_params)
        self._build_schedule_tab(tab_sched)
        self._build_upload_tab(tab_upload)
        self._build_run_tab(tab_run)

    def _build_action_bar(self):
        """底部全局操作栏：主动作按钮 + 输出工具 + 右侧状态显示，任意页签可用。"""
        bar = ttk.Frame(self.root, style="Card.TFrame", padding=(12, 8))
        bar.pack(side="bottom", fill="x")

        self.run_btn = ttk.Button(bar, text="开始检测", style="Accent.TButton",
                                  width=10, command=self._start)
        self.run_btn.pack(side="left")
        ttk.Button(bar, text="清空输出", command=self._clear_output).pack(
            side="left", padx=(8, 0))
        ttk.Button(bar, text="打开输出目录", command=self._open_out_dir).pack(
            side="left", padx=(8, 0))

        status_box = ttk.Frame(bar, style="Card.TFrame")
        status_box.pack(side="right")
        self._footer_run_var = tk.StringVar(value="最近检测：—")
        ttk.Label(status_box, textvariable=self._footer_run_var,
                  style="Status.TLabel", anchor="e").pack(anchor="e")
        self._footer_schedule_var = tk.StringVar(value="定时任务：未启动")
        ttk.Label(status_box, textvariable=self._footer_schedule_var,
                  style="Status.TLabel", anchor="e").pack(anchor="e")

    def _build_paths_tab(self, parent):
        """路径配置页：日志目录 + 输出目录 + 已处理排除开关。"""
        card = self._card(parent, "目录与日志选取")

        # 日志目录（默认转成绝对路径，避免相对路径歧义）
        log_label = ttk.Label(card, text="日志目录", style="Form.TLabel")
        log_label.grid(row=0, column=0, sticky="e", padx=(0, 10), pady=6)
        self.log_var = tk.StringVar(
            value=os.path.abspath(os.path.join(_PROJECT_ROOT, "document")))
        ttk.Entry(card, textvariable=self.log_var).grid(
            row=0, column=1, sticky="we", pady=6)
        ttk.Button(card, text="浏览…", width=8,
                   command=self._pick_log).grid(row=0, column=2,
                                                sticky="w", padx=(8, 0), pady=6)
        _ToolTip(log_label, "待处理日志所属目录（使用绝对路径），仅分析其中符合 "
                            "system.<YYYY-MM-DD>.log 命名的文件。",
                 wraplength=self._tip_wrap)
        ttk.Label(card, text="仅分析 system.<YYYY-MM-DD>.log 命名的文件；platform.* 等"
                             "其他前缀与无日期日志（如 system.log）自动排除。",
                  style="Card.Hint.TLabel", wraplength=self._hint_wrap,
                  justify="left").grid(row=1, column=1, columnspan=2,
                                       sticky="w", pady=(0, 10))

        # 输出目录
        out_label = ttk.Label(card, text="输出目录", style="Form.TLabel")
        out_label.grid(row=2, column=0, sticky="e", padx=(0, 10), pady=6)
        self.out_var = tk.StringVar(
            value=os.path.abspath(os.path.join(_PROJECT_ROOT, DEFAULT_OUT_DIR)))
        ttk.Entry(card, textvariable=self.out_var).grid(
            row=2, column=1, sticky="we", pady=6)
        ttk.Button(card, text="浏览…", width=8,
                   command=self._pick_out).grid(row=2, column=2,
                                                sticky="w", padx=(8, 0), pady=6)
        _ToolTip(out_label, "CSV 结果导出目录（使用绝对路径），不存在时自动创建。",
                 wraplength=self._tip_wrap)
        ttk.Label(card, text="CSV 结果写入该目录（异常车辆_<日期>.csv）。",
                  style="Card.Hint.TLabel", wraplength=self._hint_wrap,
                  justify="left").grid(row=3, column=1, columnspan=2,
                                       sticky="w", pady=(0, 10))

        # 已处理排除开关
        self.skip_var = tk.BooleanVar(value=True)
        skip_cb = ttk.Checkbutton(
            card,
            text="排除已处理的日志文件（内容未变化的不再重复处理，仍在写入的日志会自动重新检测）",
            variable=self.skip_var,
        )
        skip_cb.grid(row=4, column=0, columnspan=3, sticky="w", pady=(2, 2))
        _ToolTip(skip_cb, "已处理状态保存在输出目录下的 .processed.json；"
                          "定时任务不会重复处理旧日志。", wraplength=self._tip_wrap)

    def _build_params_tab(self, parent):
        """判定参数页：三个参数栅格排列，行内附默认值提示，悬停查看详细说明。"""
        card = self._card(parent, "判定参数", stretch=False)
        self.window_var = self._param_row(
            card, 0, "判定窗口", WINDOW_SECONDS, "秒",
            "出场不开闸的车，其后该秒数内收到同车“支付结果下发”视为正常；"
            "期间无下发则判为异常。")
        self.dev_var = self._param_row(
            card, 1, "最小停车时间偏差", MIN_PARK_TIME_DEVIATION, "分钟",
            "系统记录“停车时间”若比“入场→出场”实际时长至少多出该分钟数，"
            "才在“异常”列标 1（如门卫遥控放行等可疑情况）；不足该值视为取整误差。")
        self.dedup_var = self._param_row(
            card, 2, "入场去重窗口", ENTRY_DEDUP_WINDOW, "秒",
            "同车相邻两次“方向：入口”扫描间隔不超过该秒数，归并为同一次入场"
            "并取第一次时间，避免重复计次或把出场重试误判为新停车周期。")
        ttk.Label(card, text="提示：鼠标悬停参数名称可查看详细说明；"
                             "定时任务每次触发都会实时读取这里的最新值，修改后无需重启。",
                  style="Card.Hint.TLabel", wraplength=self._hint_wrap,
                  justify="left").grid(row=3, column=0, columnspan=3,
                                       sticky="w", pady=(10, 0))

    def _param_row(self, parent, row, label, default, unit, tooltip):
        """参数行：右对齐标签 + Spinbox + 紧贴的默认值提示，返回输入框变量。"""
        lbl = ttk.Label(parent, text=label, style="Form.TLabel")
        lbl.grid(row=row, column=0, sticky="e", padx=(0, 10), pady=6)
        _ToolTip(lbl, tooltip, wraplength=self._tip_wrap)
        var = tk.StringVar(value=str(default))
        box = ttk.Frame(parent, style="Card.TFrame")
        box.grid(row=row, column=1, columnspan=2, sticky="w", pady=6)
        ttk.Spinbox(box, from_=0, to=100000, width=8,
                    textvariable=var).pack(side="left")
        hint = ttk.Label(box, text=f"默认 {default} {unit}",
                         style="Card.Hint.TLabel")
        hint.pack(side="left", padx=(10, 0))
        _ToolTip(hint, tooltip, wraplength=self._tip_wrap)
        return var

    def _build_schedule_tab(self, parent):
        """定时任务页：开关 / 类型 / 间隔 / 每天时间 / 状态。"""
        card = self._card(parent, "定时任务")

        self._schedule_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            card,
            text="启用定时任务（按下方配置后台自动检测，取消勾选即停止）",
            variable=self._schedule_enabled,
            command=self._on_schedule_toggle,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        ttk.Label(card, text="任务类型", style="Form.TLabel").grid(
            row=1, column=0, sticky="e", padx=(0, 10), pady=6)
        type_box = ttk.Frame(card, style="Card.TFrame")
        type_box.grid(row=1, column=1, columnspan=2, sticky="w", pady=6)
        self._schedule_type = tk.StringVar(value="interval")
        ttk.Radiobutton(type_box, text="间隔运行", value="interval",
                        variable=self._schedule_type).pack(side="left")
        ttk.Radiobutton(type_box, text="每天固定时间", value="daily",
                        variable=self._schedule_type).pack(side="left", padx=(16, 0))

        ttk.Label(card, text="间隔", style="Form.TLabel").grid(
            row=2, column=0, sticky="e", padx=(0, 10), pady=6)
        interval_box = ttk.Frame(card, style="Card.TFrame")
        interval_box.grid(row=2, column=1, columnspan=2, sticky="w", pady=6)
        self._schedule_value = tk.StringVar(value="30")
        ttk.Spinbox(interval_box, from_=1, to=100000, width=8,
                    textvariable=self._schedule_value).pack(side="left")
        self._schedule_unit = tk.StringVar(value="分")
        ttk.Combobox(interval_box, textvariable=self._schedule_unit,
                     values=["秒", "分", "时"], width=4,
                     state="readonly").pack(side="left", padx=(8, 0))
        ttk.Label(interval_box, text="运行一次",
                  style="Card.Hint.TLabel").pack(side="left", padx=(8, 0))

        ttk.Label(card, text="每天", style="Form.TLabel").grid(
            row=3, column=0, sticky="e", padx=(0, 10), pady=6)
        daily_box = ttk.Frame(card, style="Card.TFrame")
        daily_box.grid(row=3, column=1, columnspan=2, sticky="w", pady=6)
        self._schedule_time = tk.StringVar(value="08:00")
        ttk.Entry(daily_box, textvariable=self._schedule_time,
                  width=8).pack(side="left")
        ttk.Label(daily_box, text="HH:MM，如 08:00",
                  style="Card.Hint.TLabel").pack(side="left", padx=(8, 0))

        ttk.Separator(card, orient="horizontal").grid(
            row=4, column=0, columnspan=3, sticky="ew", pady=(8, 8))

        self._schedule_status_var = tk.StringVar(value="定时任务未启动")
        self._schedule_status_lbl = ttk.Label(
            card, textvariable=self._schedule_status_var, style="Card.Status.TLabel")
        self._schedule_status_lbl.grid(row=5, column=0, columnspan=3, sticky="w")

    def _build_upload_tab(self, parent):
        """数据库上传页：开关 + Cloudflare D1 凭证 + 批量大小。

        初始值来自 config 默认（可经环境变量/.env 提供），重启后免手填。
        """
        card = self._card(parent, "Cloudflare D1 上传")

        self._upload_enabled = tk.BooleanVar(value=bool(UPLOAD_ENABLED))
        ttk.Checkbutton(
            card,
            text="启用数据库上传（把异常记录上传到 Cloudflare D1，上传成功才标记日志已处理）",
            variable=self._upload_enabled,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self._cf_account = self._entry_row(card, 1, "账户 ID", CF_ACCOUNT_ID)
        self._cf_database = self._entry_row(card, 2, "数据库 ID", CF_DATABASE_ID)

        # Token 以掩码显示，可点击“显示”核对；绝不随「导出配置」JSON 导出
        ttk.Label(card, text="API Token", style="Form.TLabel").grid(
            row=3, column=0, sticky="e", padx=(0, 10), pady=6)
        self._cf_token = tk.StringVar(value=CF_API_TOKEN)
        self._token_entry = ttk.Entry(card, textvariable=self._cf_token, show="*")
        self._token_entry.grid(row=3, column=1, sticky="we", pady=6)
        self._token_toggle_btn = ttk.Button(card, text="显示", width=6,
                                            command=self._toggle_token)
        self._token_toggle_btn.grid(row=3, column=2, sticky="w", padx=(8, 0), pady=6)

        ttk.Label(card, text="批量大小", style="Form.TLabel").grid(
            row=4, column=0, sticky="e", padx=(0, 10), pady=6)
        batch_box = ttk.Frame(card, style="Card.TFrame")
        batch_box.grid(row=4, column=1, columnspan=2, sticky="w", pady=6)
        self._db_batch = tk.StringVar(value=str(DB_BATCH_SIZE))
        ttk.Spinbox(batch_box, from_=1, to=10000, width=8,
                    textvariable=self._db_batch).pack(side="left")
        ttk.Label(batch_box, text="单次请求最多合并的记录数",
                  style="Card.Hint.TLabel").pack(side="left", padx=(8, 0))

        ttk.Separator(card, orient="horizontal").grid(
            row=5, column=0, columnspan=3, sticky="ew", pady=(8, 8))
        ttk.Label(
            card,
            text=("说明：需具备 D1:Edit 权限的 Cloudflare API Token；"
                  "三项凭证任一为空视为未配置上传（等同关闭）。\n"
                  "上传采用 INSERT OR IGNORE + 去重键，只插入新记录、永不覆盖，"
                  "重复上传自动忽略。"),
            style="Card.Hint.TLabel", wraplength=self._hint_wrap, justify="left",
        ).grid(row=6, column=0, columnspan=3, sticky="w")

    def _build_run_tab(self, parent):
        """运行输出页：只读回显区（纵向 + 横向滚动）。"""
        ttk.Label(parent, text="检测结果实时回显（只读），CSV 按日志日期写入输出目录"
                              "（异常车辆_<日期>.csv）；「开始检测」按钮在窗口底部，任意页签可用。",
                  style="Hint.TLabel", wraplength=int(720 * self._scale),
                  justify="left").pack(anchor="w", pady=(0, 6))

        box = ttk.Frame(parent)
        box.pack(fill="both", expand=True)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.output_box = tk.Text(
            box, wrap="none", height=14, state="disabled",
            bg=COLORS["card"], fg=COLORS["text"], relief="flat", bd=0,
            highlightthickness=1, highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"], selectbackground=COLORS["select"],
            insertbackground=COLORS["text"], padx=10, pady=8,
            font=self._mono_font,
        )
        self.output_box.grid(row=0, column=0, sticky="nsew")
        scroll_y = ttk.Scrollbar(box, orient="vertical",
                                 command=self.output_box.yview)
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x = ttk.Scrollbar(box, orient="horizontal",
                                 command=self.output_box.xview)
        scroll_x.grid(row=1, column=0, sticky="ew")
        self.output_box.configure(yscrollcommand=scroll_y.set,
                                  xscrollcommand=scroll_x.set)

    # ---------------- 事件处理 ----------------
    def _pick_log(self):
        choice = filedialog.askdirectory(title="选择日志目录")
        if choice:
            self.log_var.set(os.path.abspath(choice))

    def _pick_out(self):
        choice = filedialog.askdirectory(title="选择输出目录")
        if choice:
            self.out_var.set(choice)

    def _open_out_dir(self):
        """在资源管理器中打开输出目录（不存在时先创建）。"""
        out_dir = os.path.abspath(self.out_var.get().strip())
        try:
            os.makedirs(out_dir, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(out_dir)  # noqa: S606  仅本地打开目录
            else:
                import subprocess
                subprocess.Popen(["xdg-open", out_dir])
        except OSError as exc:
            messagebox.showerror("打开输出目录", f"无法打开：{exc}")

    def _toggle_token(self):
        """API Token 掩码显示 / 明文显示切换，便于核对粘贴的 Token。"""
        if self._token_entry.cget("show"):
            self._token_entry.config(show="")
            self._token_toggle_btn.config(text="隐藏")
        else:
            self._token_entry.config(show="*")
            self._token_toggle_btn.config(text="显示")

    def _append_output(self, text):
        """向只读输出区追加文本（须在主线程调用）。"""
        self.output_box.config(state="normal")
        self.output_box.insert("end", text)
        self.output_box.see("end")
        self.output_box.config(state="disabled")

    def _clear_output(self):
        self.output_box.config(state="normal")
        self.output_box.delete("1.0", "end")
        self.output_box.config(state="disabled")

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
   待处理日志所属目录（使用绝对路径），仅分析其中符合
   system.<YYYY-MM-DD>.log 命名的文件；platform.* 等其他前缀、
   不带日期的日志（如 system.log、platform.log）不进入分析。

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

费用与系统“停车时间”行紧跟在本车“方向：出口”相机扫描之后出现，解析到新的出口扫描
即重置待关联状态，出场处理仅关联本次出口扫描之后的最近一条，不会串到其他车辆的费用。"""

    def _show_help(self):
        """弹出"说明"窗口，展示参数说明。"""
        win = tk.Toplevel(self.root)
        win.title("参数说明")
        win.geometry("600x520")
        win.transient(self.root)
        win.configure(bg=COLORS["card"])
        body = ttk.Frame(win, style="Card.TFrame", padding=(14, 12, 14, 8))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        text = tk.Text(
            body, wrap="word", padx=4, pady=0, bd=0, relief="flat",
            bg=COLORS["card"], fg=COLORS["text"],
            font=(_FONT_FAMILY, 9), state="disabled",
        )
        text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scroll.set)
        text.config(state="normal")
        text.insert("1.0", self._help_text())
        text.config(state="disabled")
        ttk.Button(win, text="关闭", width=8,
                   command=win.destroy).pack(pady=(0, 10))

    def _export_config(self):
        """导出当前配置为 JSON 文件。"""
        default_name = os.path.join(_PROJECT_ROOT, "检测配置.json")
        path = filedialog.asksaveasfilename(
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
        path = filedialog.askopenfilename(
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

        self.run_btn.config(state="disabled", text="检测中…")
        self._footer_run_var.set("最近检测：检测中…")
        # 切到运行输出页，让用户立即看到回显
        self.notebook.select(self._run_tab_index)
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

        def done():
            self._finish(text, f"运行{status}")

        # 回显与控件状态回到主线程处理，避免跨线程操作 Tk
        try:
            self.root.after(0, done)
        except RuntimeError:
            done()

    def _run_detection(self, log_path, out_dir, params, skip_processed=True,
                       upload_cfg=None):
        """
        执行检测流程（纯计算，后台线程可调用），返回 (输出文本, 状态)。

        与命令行共用 cli.run_detection_round 一轮检测编排：
        先扫描完本轮全部日志并输出 CSV；开启上传时把所有异常记录汇总后
        统一分批上传，全部成功才统一标记已处理，任一批失败则本轮所有
        日志均不标记（下轮自动重试，去重键幂等）；关闭上传时逐文件立即标记。
        单个日志失败只跳过该文件，不中断本轮。
        """
        buf = StringIO()
        try:
            with redirect_stdout(buf):
                files, err = list_log_files(log_path)
                if err:
                    print(err)
                    return buf.getvalue(), "失败"
                # 开关开启但凭证不全：视为未配置上传（等同关闭），只提示一次
                upload_cfg = check_upload_cfg(upload_cfg or {})
                processed, skipped, errors, failed = run_detection_round(
                    files, out_dir, params["window"],
                    entry_dedup_window=params["dedup"],
                    min_deviation=params["deviation"],
                    skip_processed=skip_processed,
                    upload_cfg=upload_cfg,
                )
                summary = (f"\n全部处理完成：本次处理 {processed} 个日志，"
                           f"跳过已处理 {skipped} 个。")
                if errors:
                    summary += f"读取失败 {errors} 个（未标记已处理，下轮自动重试）。"
                if failed:
                    summary += f"上传失败 {failed} 个（未标记已处理，下轮自动重试）。"
                print(summary)
        except Exception as exc:  # 界面层兜底，避免后台线程静默崩溃
            buf.write(f"\n发生错误：{exc}\n")
            return buf.getvalue(), "出错"
        status = "完成" if not errors and not failed else "完成（有失败项）"
        return buf.getvalue(), status

    def _finish(self, text, status):
        """把输出写到文本框，并恢复按钮状态（须在主线程调用）。"""
        self._append_output(text)
        self.run_btn.config(state="normal", text="开始检测")
        self._footer_run_var.set(f"最近检测：{status}  {time.strftime('%H:%M:%S')}")
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
        """刷新定时任务状态标签与底部状态栏（须在主线程调用）。"""
        if self._scheduler.running:
            nxt = self._scheduler.next_run
            nxt_txt = nxt.strftime("%Y-%m-%d %H:%M:%S") if nxt else "待定"
            self._schedule_status_var.set(f"定时任务运行中，下次触发：{nxt_txt}")
            self._schedule_status_lbl.config(style="Card.StatusOK.TLabel")
            short = nxt.strftime("%m-%d %H:%M") if nxt else "待定"
            self._footer_schedule_var.set(f"定时任务：运行中，下次 {short}")
        else:
            self._schedule_status_var.set("定时任务未启动")
            self._schedule_status_lbl.config(style="Card.Status.TLabel")
            self._footer_schedule_var.set("定时任务：未启动")

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
        try:
            self.root.after(0, lambda: self._append_output(text))
        except RuntimeError:
            pass

    # ---------------- 应用图标（托盘 + 主窗口共用） ----------------
    def _make_icon_image(self, size=64):
        """生成蓝底白 P 的图标图片（PIL Image，供托盘与主窗口复用）。

        边距与字号按 64px 基准等比缩放，可用更大尺寸渲染出更清晰的多尺寸 ico。
        """
        from PIL import Image, ImageFont, ImageDraw
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # 蓝色正方形底（四周留 1px 透明边，避免贴边不清晰）
        margin = max(1, round(size / 64))
        draw.rectangle((margin, margin, size - margin - 1, size - margin - 1), fill="#2f6fed")
        # 优先使用系统字体放大 P，失败则退回默认字体（默认字体不支持中文，故用 ASCII）
        try:
            font = ImageFont.truetype("arial.ttf", round(size * 55 / 64))
        except (OSError, Exception):
            font = ImageFont.load_default()
        # 把文本水平垂直居中
        text = "P"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pos = ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1])
        draw.text(pos, text, fill="white", font=font)
        return img

    def _write_icon_file(self):
        """把应用图标渲染为多尺寸 .ico 文件（写入系统临时目录，返回路径）。

        以 256px 渲染、由 PIL 缩放出各尺寸，任务栏 16/32px 下比 64px 直接绘制更清晰；
        iconphoto 生成的图标在部分 Windows 系统上不被任务栏采用，iconbitmap 才可靠。
        """
        import tempfile
        img = self._make_icon_image(256)
        path = os.path.join(tempfile.gettempdir(), "parkcheck_app.ico")
        img.save(
            path, format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
        return path

    def _set_window_icon(self):
        """为主窗口设置应用图标（与托盘一致的蓝底白 P）。

        iconphoto 一并设置，保证后续 Toplevel（如说明窗口）默认继承同一图标；
        Windows 下再以多尺寸 .ico 执行 iconbitmap，任务栏才能显示应用图标而非 Python 图标。
        """
        try:
            from PIL import ImageTk
        except ImportError:
            return
        icon_img = ImageTk.PhotoImage(self._make_icon_image())
        # 保留引用，避免被垃圾回收导致图标消失
        self._window_icon = icon_img
        self.root.iconphoto(True, icon_img)
        if sys.platform == "win32":
            try:
                self.root.iconbitmap(self._write_icon_file())
            except Exception:
                pass

    # ---------------- 系统托盘 ----------------
    def _setup_tray(self):
        """初始化系统托盘图标（pystray）；不可用时回退为普通最小化。"""
        try:
            import pystray
        except ImportError:
            return

        class _TrayIcon(pystray.Icon):
            def _notify(self, message, title=None):
                # pystray 的 Windows 通知只发文本（NIF_INFO 未带 dwInfoFlags/hBalloonIcon），
                # 通知弹窗因此显示系统默认图标；这里补上托盘图标句柄。
                try:
                    from pystray._util import win32
                    self._assert_icon_handle()
                    self._message(
                        win32.NIM_MODIFY,
                        win32.NIF_INFO,
                        szInfo=message,
                        szInfoTitle=title or self.title or "",
                        dwInfoFlags=0x00000004 | 0x00000020,  # NIIF_USER | NIIF_LARGE_ICON
                        hBalloonIcon=self._icon_handle,
                    )
                except Exception:
                    super()._notify(message, title)

        img = self._make_icon_image()
        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", self._tray_show),
            pystray.MenuItem("退出程序", self._tray_quit),
        )
        self._tray_icon = _TrayIcon("parkcheck", img, "停车场异常车辆检测", menu)
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
    _set_app_user_model_id()       # 任务栏与通知按本应用身份显示图标，而非 python.exe
    _enable_windows_dpi_awareness()  # 高分屏文字清晰，须在创建窗口前调用
    root = tk.Tk()
    CheckGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
