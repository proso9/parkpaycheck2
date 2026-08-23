# AGENTS.md

面向在此仓库协助开发的 AI 与协作者的工程指南。

## 项目概述

停车场系统**异常车辆检测**工具：从系统日志中找出“出场不开闸”且“300 秒内无支付结果下发”的异常车辆，并输出结果。

## 目录结构

```
parkpaycheck_v2/
├── AGENTS.md                       # 本文件
├── README.md                       # 项目介绍、使用说明与依赖安装命令
├── check_unopened_gate.py          # 命令行入口（薄封装，调用 parkcheck.cli）
├── check_gui.py                    # 图形界面入口（tkinter 分页 + APScheduler + 系统托盘）
├── parkcheck/                      # 检测包
│   ├── __init__.py                 # 公共 API 聚合导出
│   ├── config.py                   # 常量与默认参数
│   ├── parser.py                   # 日志解析
│   ├── detector.py                 # 异常判定与入场反查
│   ├── output.py                   # 结果输出（控制台 + CSV）
│   ├── scheduler.py                # 定时任务（APScheduler 封装）
│   └── cli.py                      # 命令行入口
├── tests/
│   ├── test_check_unopened_gate.py # 核心逻辑无依赖断言式测试
│   └── test_scheduler.py           # 定时任务模块测试（依赖 apscheduler）
├── document/                       # 日志样例(输入, 不入库)
│   └── system.<日期>.log
└── output/                         # 脚本导出的 CSV(不入库, 自动创建)
    └── 异常车辆_<日期>.csv
```

## 检测流程

整个流程在 `parkcheck` 包内完成，入口脚本仅做薄封装：

1. **record_a**（`parser.parse_log`）：解析含“出场处理”且“开闸结果：不开闸”的行，取时间 `T` 与车牌。
2. **record_b**（`parser.parse_log`）：解析含“==>支付结果下发：”的行，取时间与 JSON 中的 `carNumber`。
3. **判定**（`detector.find_anomalies`）：对每条记录A，在记录B中找**车牌相同且时间 ∈ [T, T+300秒]** 的记录；找不到则判为异常。
4. **入场反查**（`detector.find_anomalies`）：以相机`方向：入口`扫描（含重复上传）作为入场事件来源，`入场车牌号`行兜底；同一辆车 5 秒内的重复入口扫描归并为同一次入场并取第一次，本次出场取之前最近一次入场事件的时间；无匹配入场用 `-` 标记（窗口 `ENTRY_DEDUP_WINDOW` 可调）。
5. **可疑标记**（`detector.compute_anomaly`）：解析出场相机扫描后的"停车时间:X天,剩余:Y分钟"，换算成总分钟数；与"入场→出场"实际时长比较，仅当系统停车时间**明显**大于实际时长（至少多出 `MIN_PARK_TIME_DEVIATION` 分钟，如被门卫遥控放行）时在输出中标记"异常"为 1，否则为 0。

各模块职责：`config.py` 存放关键词与参数默认值，`parser.py` 负责时间/车牌/费用/入场提取与拆行，`detector.py` 负责异常判定与入场反查，`output.py` 负责控制台与 CSV 输出。

关键实现点：

- 日志中部分业务行（如“出场处理…”）**不带时间戳**，需继承上方最近一条带时间戳行的时刻。
- 车牌匹配前需**归一化**（去空格、转大写），确保 `carNumber`、入场车牌与《…》车牌严格对应、不串号。
- 支付下发 `carNumber` 优先 JSON 解析，失败降级为正则兜底。
- 入场以相机`方向：入口`为准（含“重复上传”等重复上报），避免“出场不开闸重试”被误判为新的停车周期。
- 出场记录会关联“用户需支付费用”（`用户需支付费用:金额`）。因费用行与出场相机识别**同时出现**，故直接按日志顺序取最近一条（仅要求其时刻不晚于本次出场，不设关联窗口）；异常输出含该字段。
- 出场记录会关联系统“停车时间”（`停车时间:X天,剩余:Y分钟`）。同理直接按时间取最近一条，用于与“入场→出场”实际时长比较判断可疑标记。
- 非法时间、缺失车牌均容错跳过。

输出列：`入场时间, 出场时间, 车牌号, 用户需支付费用, 异常`（入场时间找不到显示 `-`，无关联费用显示 `-`；“异常”为 1 表示系统停车时间明显大于实际时长，可疑）。

## 命令行用法

```bash
# 处理 document/ 下所有日志，默认输出到 output/
python check_unopened_gate.py

# 指定单个日志
python check_unopened_gate.py document\system.2026-08-22.log

# 调整判定窗口(秒)与输出目录
python check_unopened_gate.py -w 300 -o output

# 也可通过模块方式运行
python -m parkcheck.cli -w 300 -o output
```

参数均在 `parkcheck/config.py` 定义默认值，并在 `parkcheck/cli.py` 的 argparse 中提供覆盖，未硬编码。

## 图形界面与定时任务

```bash
# 启动图形界面（含路径/参数/定时任务配置，配置可导入导出 JSON）
python check_gui.py
```

图形界面（`check_gui.py`）在命令行能力基础上增加**分页配置**与**系统托盘后台运行**：

- **分页配置**（`ttk.Notebook`）：路径配置 / 判定参数 / 定时任务 / 运行输出 四个页签，配置相互隔离、互不干扰。
- **定时任务**（基于 APScheduler，见 `parkcheck/scheduler.py`）：
  - 勾选「启用定时任务」即按配置在后台自动定时检测，取消勾选即停止。
  - 支持两种方式：
    - **间隔运行**：每隔 N 秒/分/时运行一次；
    - **每天固定时间**：每天 HH:MM（如 08:00）运行一次。
  - 定时检测每次触发会**实时读取**当前界面的日志目录、输出目录与判定参数，修改后无需重启即生效。
  - 状态栏显示「下次触发」时间。
- **系统托盘后台运行**（pystray）：
  - 点击窗口关闭按钮 → 自动收纳到右下角系统托盘（`root.withdraw()`），后台定时任务继续运行；
  - 托盘菜单：「显示主窗口」恢复窗口、「退出程序」完全退出；
  - 托盘不可用（未安装 pystray/Pillow）时回退为最小化到任务栏；
  - 菜单栏「配置 → 退出程序」为显式退出路径；完全退出时会停止调度器并移除托盘图标。
- 定时配置随「导出/导入配置」JSON 一同保存恢复（字段：`schedule_enabled / schedule_type / schedule_value / schedule_unit / schedule_time`）。
- 调度逻辑封装在 `SchedulerManager`（`start_interval` / `start_daily` / `stop` / `running` / `next_run`），与手动检测共用同一检测流程，可独立复用。

## 测试

```bash
# 核心逻辑测试（无第三方依赖）
python -m tests.test_check_unopened_gate

# 定时任务模块测试（依赖 apscheduler）
python -m tests.test_scheduler
```

两套测试均用断言校验，退出码 0 表示全部通过。

## 约定与注意事项

- 输出结果写入 `output/`，**不要**写入 `document/`。
- `document/`、`output/`、`tests/` 均已加入 `.gitignore`。
- 日志为 `system.<YYYY-MM-DD>.log` 命名，输出 CSV 用同日期命名（`异常车辆_<YYYY-MM-DD>.csv`）。
- 修改判定逻辑后请补充/调整测试并确保全部通过。
- 图形界面依赖第三方库：定时任务 `apscheduler`、系统托盘 `pystray`/`Pillow`（安装命令见 `README.md`），核心检测逻辑仍保持无第三方依赖。
- 代码注释、文档一律使用简体中文。